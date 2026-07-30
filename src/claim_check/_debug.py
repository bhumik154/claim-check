"""Optional raw-payload capture, for debugging hook wiring.

The official plugin documentation contradicts itself on at least one payload
field name and omits others entirely, so the only reliable way to know what a
hook actually receives on a given Claude Code version is to capture it. This
ships as a permanent affordance rather than a throwaway capture plugin,
because the question recurs every time the payload schema changes.
"""

import os
import time
from pathlib import Path


def dump_payload(event: str, raw: str) -> None:
    """Writes the raw hook payload to $CLAIM_CHECK_DEBUG_DUMP, if set.

    Silent no-op when the variable is unset, which is the normal case.

    Never raises, under any circumstance. A debugging aid that can break the
    hook it instruments is worse than no debugging aid: this runs inside a
    PreToolUse hook where an unhandled exception is a nonzero exit.
    """
    target = os.environ.get("CLAIM_CHECK_DEBUG_DUMP")
    if not target:
        return
    try:
        directory = Path(target)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"{event}-{time.time_ns()}.json"
        destination.write_text(raw, encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - deliberate catch-all; see docstring
        pass
