"""Reads the last assistant message out of a Claude Code transcript.

Only the tail of the file is ever inspected. Transcripts in this project's
own sessions reached 15 MB, and the Stop hook runs at the end of every turn,
so reading the whole file is not an option.

Every failure resolves to None: a missing file, an unreadable one, a corrupt
line, a message older than the window. The Stop hook reads None as "no claim
to check" and stays silent. A transcript we cannot read is not evidence that
anything is wrong.
"""

import json
from pathlib import Path
from typing import Optional

# Enough to cover a long final message plus the records around it, small
# enough that reading it costs nothing next to interpreter startup.
DEFAULT_WINDOW_BYTES = 256 * 1024


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

        message = record.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue

        blocks = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(block for block in blocks if isinstance(block, str))
        if text.strip():
            return text

    return None


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
