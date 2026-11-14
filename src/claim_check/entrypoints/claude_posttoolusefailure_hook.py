"""Claude Code PostToolUseFailure hook: records failed verification runs.

Claude Code sends a Bash call that exits nonzero here, never to PostToolUse,
with `error` reading "Exit code N" followed by everything the call printed.
Before this hook existed claim-check had never seen a command fail, so a
claim that `make check` passes could not be held against the run that failed.

Observation only, like PostToolUse: emits nothing, blocks nothing, and exits 0
whatever happens. Blame for the failure is decided by run_log.py, and only
where it can be pinned on one command.
"""

import json
import sys
from typing import Optional, Sequence

from .._debug import dump_payload
from .._payload import bash_command, effective_dir, leading_cd_ok, session_id, tool_failure
from ..run_log import record_call


def _run(stdin_text: Optional[str]) -> int:
    if stdin_text is None and sys.stdin.isatty():
        return 0

    raw = stdin_text if stdin_text is not None else sys.stdin.read()
    dump_payload("PostToolUseFailure", raw)

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    command = bash_command(payload)
    if command is None:
        return 0
    failure = tool_failure(payload)
    if failure is None:
        return 0
    exit_code, output = failure

    # An interrupted call stopped partway: what it printed proves nothing,
    # but it is still the most recent run, so it is recorded as such.
    outcome = "interrupted" if payload.get("is_interrupt") is True else "failed"
    record_call(
        effective_dir(payload, command), session_id(payload), command, output,
        outcome, exit_code, cd_ok=leading_cd_ok(payload, command),
    )
    return 0


def main(stdin_text: Optional[str] = None, argv: Optional[Sequence[str]] = None) -> int:
    try:
        return _run(stdin_text)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all backstop
        print(f"claim-check: WARNING - could not record a failed run ({exc!r})", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
