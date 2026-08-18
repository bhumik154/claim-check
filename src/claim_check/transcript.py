"""Reads Claude Code transcripts.

Two readers with different jobs. `last_assistant_text` inspects only the tail
of the file, because it runs at the end of every turn and transcripts in this
project's own sessions reached 21 MB. `iter_records` reads the whole file, and
is affordable only because its callers reach it on a small fraction of turns:
measured at 1.5 times per session, against 85ms for the largest transcript on
disk.

Every failure resolves to nothing: a missing file, an unreadable one, a corrupt
line, a message older than the window. Callers read that as "no claim to check"
and stay silent. A transcript we cannot read is not evidence that anything is
wrong.
"""

import datetime
import json
import time
from pathlib import Path
from typing import Iterator, Optional

# Enough to cover a long final message plus the records around it, small
# enough that reading it costs nothing next to interpreter startup.
DEFAULT_WINDOW_BYTES = 256 * 1024

# The Stop hook has a 10 second budget and spawning the interpreter already
# costs roughly 300ms. 2 seconds covers a transcript of several hundred MB at
# the measured parse rate, so in practice the replay always finishes.
DEFAULT_REPLAY_BUDGET_S = 2.0

# Checking the clock on every line would cost more than the parse it guards.
_BUDGET_CHECK_EVERY = 512


class ReplayAborted(Exception):
    """The replay ran out of wall clock budget.

    Raised rather than returning a short iterator on purpose. A truncated
    replay produces a plausible but wrong count, and a confidently wrong
    number is the exact failure this design avoids. The caller turns this
    into "no statistics available".
    """


def last_assistant_text(path, max_bytes: int = DEFAULT_WINDOW_BYTES) -> Optional[str]:
    """The prose of the most recent assistant message, or None.

    Scans backwards for a record of type "assistant" that carries at least
    one non-empty text block. Records holding only tool calls are skipped,
    since they contain no prose and therefore no claim. Sidechain records are
    skipped too: a subagent talking is not the main agent reporting to the
    user.
    """
    lines = _tail_lines(path, max_bytes)
    if lines is None:
        return None

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            # A truncated fragment from seeking mid-file, or a corrupt line.
            continue
        if not isinstance(record, dict) or record.get("type") != "assistant":
            continue
        if record.get("isSidechain") is True:
            continue

        text = assistant_text(record)
        if text.strip():
            return text

    return None


def assistant_text(record: dict) -> str:
    """The joined prose of an assistant record, or "" if it carries none.

    Text blocks only. A record holding just tool calls contains no prose and
    therefore no claim.
    """
    if not isinstance(record, dict):
        return ""
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    blocks = [
        block.get("text", "")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(block for block in blocks if isinstance(block, str))


def _tail_lines(path, max_bytes: int):
    """The last `max_bytes` of the file, split into lines, or None.

    The first line is dropped whenever the read started mid-file, because
    seeking to a byte offset lands in the middle of a record and that
    fragment is not parseable.
    """
    try:
        target = Path(path)
    except TypeError:
        return None

    try:
        size = target.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None

    start = max(0, size - max_bytes)
    try:
        with target.open("rb") as stream:
            if start:
                stream.seek(start)
            raw = stream.read()
    except OSError:
        # Missing, a directory, locked, or permission-denied.
        return None

    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if start and lines:
        lines = lines[1:]
    return lines


def iter_records(path, budget_s: Optional[float] = None) -> Iterator[dict]:
    """Every parseable record in the file, in order.

    Unlike `last_assistant_text` this reads the whole file. That is only
    affordable because callers reach it on a small fraction of turns; see
    the module docstring.

    Blank lines, corrupt lines and a truncated final line are skipped rather
    than treated as the end of the file. A missing or unreadable file yields
    nothing, consistent with every other reader here: unreadable is unknown,
    never false.
    """
    try:
        target = Path(path)
    except TypeError:
        return

    deadline = None if budget_s is None else time.monotonic() + budget_s
    try:
        with target.open("r", encoding="utf-8", errors="replace") as stream:
            for index, line in enumerate(stream):
                if deadline is not None and index % _BUDGET_CHECK_EVERY == 0:
                    if time.monotonic() >= deadline:
                        raise ReplayAborted(f"replay exceeded {budget_s}s")
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def record_timestamp(record: dict) -> Optional[float]:
    """The record's timestamp as a Unix float, or None."""
    if not isinstance(record, dict):
        return None
    raw = record.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.timestamp()
