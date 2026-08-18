"""Claude Code Stop hook: checks a claim made in conversation, not in a commit.

The PreToolUse hook catches a false test count in a commit message. This
catches the far commoner case: an agent asserting "all tests pass" in a
summary, having run nothing.

**It declares and never blocks.** A false positive here is much more
intrusive than one on `git commit`, where the user asked for an action and
expects a gate. Blocking stays deferred until this has produced a measured
false-positive rate.

**It never runs pytest.** It compares against evidence the PostToolUse hook
recorded from runs the agent already made. Re-running a suite at the end of
every turn is what would get this uninstalled inside a day.

The gate for a FINDING of falsehood is narrow on purpose. A finding requires
all of:
  - stop_hook_active false
  - a claim in the final assistant message, once quotations are stripped
  - fresh, same-session evidence
  - from a run that was not scope-narrowing
  - that reported no errors
  - and that contradicts the claim
A turn with no claim at all, or a true claim, still produces no output. But a
claim that fails any of the evidence conditions above - no evidence, scoped
evidence, evidence from another session, or stale evidence - is not silent
either: it is reported as unverified, distinct from a finding of falsehood.
Measured across 43 real sessions, claims appear on 1.8% of turns, and about
half of those claims lack usable evidence and now speak up as unverified
rather than staying quiet.
"""

import json
import sys
from typing import Optional, Sequence

from .._debug import dump_payload
from .._payload import resolve_cwd, session_id
from ..compare import compare_claims
from ..conversation import extract_claims
from ..evidence import counts_from_record, load_fresh
from ..session_stats import ratio_suffix
from ..transcript import last_assistant_text

# Appended to every finding. The one outcome this must never encourage is an
# agent deleting an assertion so an earlier sentence becomes true, so the
# instruction is a fixed constant rather than generated text, and a test
# asserts it appears in every finding.
CORRECTION_INSTRUCTION = (
    "Correct the statement to match the observed result. "
    "Do not modify tests or source code to make the earlier statement true."
)


def _note(message: str) -> str:
    return json.dumps({"systemMessage": message})


def _cause_sentence(record) -> str:
    if record is None:
        # `load_fresh` collapses four distinct reasons into this one None:
        # no record at all, a malformed record, a TTL expiry, or a
        # fingerprint mismatch. In the TTL case a whole-suite run really was
        # recorded this session, so the sentence must not deny that a run
        # exists; it can only say the run on hand is not usable, without
        # claiming to know which of the four reasons is why.
        return (
            "No usable whole-suite test run for this claim: either none was "
            "recorded in this session, or the most recent one is too old, unreadable, "
            "or recorded against a different state of the tree."
        )
    return "The most recent test run recorded in this session was narrowed, so it covered less than the whole suite."


def _run(stdin_text: Optional[str]) -> int:
    if stdin_text is None and sys.stdin.isatty():
        return 0

    raw = stdin_text if stdin_text is not None else sys.stdin.read()
    dump_payload("Stop", raw)

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    # Recursion guard, checked before anything else. Claude Code sets this
    # while a Stop hook is already in flight; ignoring it risks a loop.
    if payload.get("stop_hook_active") is True:
        return 0

    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str):
        return 0

    text = last_assistant_text(transcript_path)
    if not text:
        return 0

    # Cheap prefilter before any parsing. Every claim shape contains "pass",
    # so one case-insensitive scan drops the ~98% of turns that assert
    # nothing about tests.
    if "pass" not in text.lower():
        return 0

    claims = extract_claims(text)
    if not claims:
        return 0

    claim = claims[0]
    record = load_fresh(resolve_cwd(payload), session_id(payload))

    if record is None or record.get("scoped") is True:
        # No usable evidence. Reported, never as a finding of falsehood: the
        # claim may be perfectly true and simply unchecked. The cause leads,
        # because the cause is the part the reader can act on.
        print(_note(
            f'claim-check: could not verify "{claim.raw_text}". '
            f"{_cause_sentence(record)}{ratio_suffix(payload)}"
        ))
        return 0

    counts = counts_from_record(record)
    if counts is None or counts.errors:
        # An error means some test never ran, so the true denominator is
        # unknown. Blocking a commit on that is defensible; nagging about it
        # in conversation is not.
        return 0

    verdict = compare_claims([claim], counts)
    if verdict.status != "mismatch":
        return 0

    print(_note(
        f"claim-check: {verdict.message}, according to the last full test run "
        f"in this session ({counts.raw_summary_line.strip()}). "
        f"{CORRECTION_INSTRUCTION}"
    ))
    return 0


def main(stdin_text: Optional[str] = None, argv: Optional[Sequence[str]] = None) -> int:
    try:
        return _run(stdin_text)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all backstop
        # Nothing this hook can hit internally is evidence a claim is wrong.
        print(f"claim-check: WARNING - end-of-turn check failed ({exc!r})", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
