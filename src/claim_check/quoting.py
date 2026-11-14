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

Every rule here only marks text it is sure of. A quotation that is never
closed marks nothing, a code span never crosses a blank line, and straight
single quotes are not quotation at all. The 0.8.0 smoke test showed why:
each guess about where a quotation ends hid a real claim somewhere, and an
earlier, stale claim got checked in its place.

Everything is linear in the length of the text. This runs inside hooks with a
time budget, and patterns that matched whitespace across line breaks took
minutes on a message of blank lines.
"""

import bisect
import re
from typing import List, Tuple

Region = Tuple[int, int]

# A fenced block, closed or not, for chat text. An unterminated fence there
# swallows everything after it: a chat turn pastes output constantly, and the
# claim selection refuses to fall back to an earlier claim if this hides a
# later one.
_FENCED = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)

# A closed fenced block. Commit messages only mark what is certainly quoted.
_FENCED_CLOSED = re.compile(r"```.*?```", re.DOTALL)

# A quotation of someone or something else. Spaces and tabs only before the
# marker: \s let the pattern start on every blank line and run to the next
# ">" or not at all, which is quadratic in a message of blank lines.
_BLOCKQUOTE = re.compile(r"^[ \t]*>.*$", re.MULTILINE)

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
# a plain "Tests: 22 passed" is someone stating a result. The count is checked
# by a lookahead so the line is scanned once, not once per split point.
_OTHER_RUNNER_TALLY = re.compile(
    r"^[ \t]*(?:Test Files|Tests|Test Suites|Suites|Snapshots|Time)[ \t]*:?[ \t]+"
    r"(?=[^\n]*?\b\d+[ \t]+(?:passed|failed|skipped|todo|pending|total)\b)"
    r"[^\n]*(?:\(\d+\)|\b\d+[ \t]+total)[ \t]*\r?$",
    re.MULTILINE | re.IGNORECASE,
)

_PARAGRAPH_BREAK = re.compile(r"\r?\n[ \t]*\r?\n")
_BACKTICK_RUN = re.compile(r"`+")
_DOUBLE_OR_BREAK = re.compile(r"[\"“”„]|\r?\n[ \t]*\r?\n")
_CURLY_SINGLE_OR_NEWLINE = re.compile(r"[‘’\n]")


def strip_quoted_blocks(text: str) -> str:
    """Removes whole blocks of quoted material from chat text: fenced and
    indented code, blockquotes, and other runners' tallies.

    Order matters: fenced blocks go first, so their contents cannot be
    partially matched by the narrower patterns afterwards.
    """
    text = _FENCED.sub("\n", text)
    text = _BLOCKQUOTE.sub("", text)
    text = _removed(text, _indented_lines(text))
    text = _OTHER_RUNNER_TALLY.sub("", text)
    return text


def quoted_block_regions(text: str) -> List[Region]:
    """Block quotation in a commit message, marked rather than removed.

    Deleting joins the text on either side, and the claim patterns match
    across the join: "22", an indented line, then "passed" becomes "22", a
    blank line, "passed", a claim nobody wrote. Marking can only ever remove
    a claim.
    """
    regions = [m.span() for m in _FENCED_CLOSED.finditer(text)]
    regions += [m.span() for m in _BLOCKQUOTE.finditer(text)]
    regions += _indented_lines(text)
    regions += [m.span() for m in _OTHER_RUNNER_TALLY.finditer(text)]
    return regions


def quoted_regions(text, code_spans: bool = True) -> List[Region]:
    """Sorted, non-overlapping (start, end) spans of quoted text, quote marks
    included.

    - A code span is a run of N backticks closed by the next run of exactly N
      in the same paragraph, as in Markdown. An unmatched run is literal text.
    - Double quotes, straight, curly or German, pair within a paragraph, so a
      quotation can wrap across lines. One never closed marks nothing.
    - Curly single quotes pair on one line. Straight single quotes are never
      quotation: the same character is the apostrophe in "'cause" and "'90s".

    Quote marks inside a code span are part of the code span.

    `code_spans=False` leaves code spans themselves out of the result, while
    still ignoring quote marks inside them. A claim that a command passed
    names the command in a code span, "`make check` passes", and there the
    span is the claim's subject, not a quotation.
    """
    if not isinstance(text, str) or not text:
        return []
    code = _code_spans(text)
    doubles = _double_quotes(text, code)
    singles = _single_quotes(text, merged(code + doubles))
    if not code_spans:
        return merged(doubles + singles)
    return merged(code + doubles + singles)


def code_spans(text) -> List[Region]:
    """The Markdown code spans in `text`, as (start, end) with backticks."""
    if not isinstance(text, str) or not text:
        return []
    return _code_spans(text)


def merged(regions: List[Region]) -> List[Region]:
    """Sorted union of possibly overlapping regions. A code span inside a
    double quote, `"see `x` here"`, is one quotation."""
    result: List[Region] = []
    for start, end in sorted(regions):
        if result and start < result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def _removed(text: str, regions: List[Region]) -> str:
    if not regions:
        return text
    parts, last = [], 0
    for start, end in regions:
        parts.append(text[last:start])
        last = end
    parts.append(text[last:])
    return "".join(parts)


def _indented_lines(text: str) -> List[Region]:
    """The lines of Markdown's indented code blocks.

    As in Markdown, a block starts only after a blank line or at the top, and
    runs through indented and blank lines. An indented line straight after
    text is a continuation, and git commit bodies wrap bullets that way:
    "  - before the fix only 3 passed, now" then "    all 5 tests pass".
    """
    regions = []
    position = 0
    previous_blank = True
    in_block = False
    for line in text.split("\n"):
        end = position + len(line)
        blank = not line.strip()
        indented = line.startswith("    ") or line.startswith("\t")
        if in_block and not (indented or blank):
            in_block = False
        if not in_block and indented and not blank and previous_blank:
            in_block = True
        if in_block and not blank:
            regions.append((position, end))
        previous_blank = blank
        position = end + 1
    return regions


def _code_spans(text: str) -> List[Region]:
    breaks = [m.start() for m in _PARAGRAPH_BREAK.finditer(text)]
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
        if bisect.bisect_right(breaks, start) != bisect.bisect_right(breaks, runs[closer][0]):
            # The match is in another paragraph, so nothing in this one is.
            index += 1
            continue
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
    """Every closed double-quoted region, nested ones included.

    Straight quotes are paired by which way they face, not just in order.
    Quoting a quotation is common in exactly the text this reads, "people
    don't write "22 passed" in prose", and pairing in order there leaves the
    inner example outside every quote. A mark after a space facing a word
    opens; a mark after a word facing a space or punctuation closes. A mark
    that could only close, with nothing open, is ignored: that is the inch
    mark in `5" screen`.

    Whatever is still open at the end of a paragraph is discarded, and only
    the quotations closed inside it count.
    """
    outside = _Outside(code)
    regions = []
    stack = []  # (start, kind): kind is '"', curly or German
    for m in _DOUBLE_OR_BREAK.finditer(text):
        position = m.start()
        if not outside(position):
            continue
        mark = m.group()
        if len(mark) > 1:
            stack.clear()
            continue
        top = stack[-1][1] if stack else None
        if mark == "„":
            stack.append((position, "german"))
            continue
        if mark == "“":
            if top == "german":
                regions.append((stack.pop()[0], position + 1))
            else:
                stack.append((position, "curly"))
            continue
        if mark == "”":
            if top in ("curly", "german"):
                regions.append((stack.pop()[0], position + 1))
            continue
        before = text[position - 1] if position else " "
        after = text[position + 1] if position + 1 < len(text) else " "
        can_open = (before.isspace() or before in "([{") and not after.isspace()
        # After an opening bracket a quote can only open: ("x") was missed.
        can_close = not before.isspace() and before not in "([{" and not (after.isalnum() or after == "_")
        if can_open and not can_close:
            stack.append((position, '"'))
        elif top == '"':
            regions.append((stack.pop()[0], position + 1))
        elif not can_close:
            stack.append((position, '"'))
    return regions


def _single_quotes(text: str, quoted: List[Region]) -> List[Region]:
    """Curly single quotation on one line. The closer must not be followed by
    a letter, since the same character is the apostrophe in "it’s"."""
    outside = _Outside(quoted)
    regions = []
    opened = None
    for m in _CURLY_SINGLE_OR_NEWLINE.finditer(text):
        position = m.start()
        mark = m.group()
        if mark == "\n":
            opened = None
            continue
        if not outside(position):
            continue
        if mark == "‘":
            opened = position
            continue
        after = text[position + 1] if position + 1 < len(text) else ""
        if opened is not None and not (after.isalnum() or after == "_"):
            regions.append((opened, position + 1))
            opened = None
    return regions
