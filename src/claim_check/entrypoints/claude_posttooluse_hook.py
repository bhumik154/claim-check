"""Claude Code PostToolUse hook: records real test runs as evidence.

Observation, not enforcement. This hook **emits nothing and blocks nothing**.
When the agent runs pytest through the Bash tool, its output arrives here for
free, already produced - so the counts can be captured without paying for a
second suite run.

That is what makes checking a claim at the end of a turn affordable at all.
The alternative, re-running pytest on every turn to verify whatever was said,
costs a full suite run per turn and would be removed within a day.

Exits 0 unconditionally and writes nothing to stdout: a PostToolUse hook runs
after the tool has already executed, so there is nothing to allow or deny,
and stdout on exit 0 is parsed as structured hook output.
"""

import sys
from typing import Optional, Sequence

from .._debug import dump_payload
from .._payload import effective_dir, leading_cd_ok, session_id, shell_command, tool_output, tool_stdout
from ..evidence import record_run
from ..run_log import record_call

import json


def _run(stdin_text: Optional[str]) -> int:
    if stdin_text is None and sys.stdin.isatty():
        # Never reached under Claude Code, which always pipes the payload in;
        # this only stops a developer running the command bare in a terminal
        # from hanging forever on a read that will never return.
        return 0

    raw = stdin_text if stdin_text is not None else sys.stdin.read()
    dump_payload("PostToolUse", raw)

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    call = shell_command(payload)
    if call is None:
        return 0
    command, tool = call
    if tool != "Bash":
        # A PowerShell call counts as the latest run of what it ran, so a
        # rerun there that passed supersedes a Bash failure. Nothing more:
        # neither test evidence nor blame is read from PowerShell syntax.
        record_call(
            effective_dir(payload, command), session_id(payload), command, "", "ok",
            cd_ok=leading_cd_ok(payload, command), blame=False,
        )
        return 0

    where = effective_dir(payload, command)
    stdout = tool_stdout(payload)
    if stdout:
        # record_run itself decides whether this was a test run: it returns
        # None when the output holds no pytest summary line. Deciding from
        # the output rather than by pattern-matching the command string means
        # a wrapper ("make test", "poetry run pytest") is recognised on its
        # merits. Filed under where the suite ran, not where the Bash tool
        # was: a run after `cd /repo &&` belongs to /repo, and fingerprinting
        # the parent folder instead would go stale on edits to any other
        # project in it.
        record_run(where, session_id(payload), command, stdout)

    # Every verification command, for claims that one passed. The call
    # succeeded, but through `| tail` that says nothing about the tool, so
    # what it printed is kept to judge by.
    record_call(
        where, session_id(payload), command, tool_output(payload) or "", "ok",
        cd_ok=leading_cd_ok(payload, command),
    )
    return 0


def main(stdin_text: Optional[str] = None, argv: Optional[Sequence[str]] = None) -> int:
    try:
        return _run(stdin_text)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all backstop
        # Recording evidence is a convenience. Nothing it can hit is worth
        # surfacing an error into the user's session, let alone a nonzero
        # exit from a hook.
        print(f"claim-check: WARNING - could not record test evidence ({exc!r})", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
