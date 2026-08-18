"""Session-level statistics about test-count claims.

Answers one question for a single session: of the claims made, how many had a
whole-suite test run behind them, and for the rest, why not.

Nothing is stored. The numbers are recomputed from the transcript on demand,
because the alternative is a counter that can go stale and then lies
confidently, which is the failure this whole project exists to prevent.

The reported quantity is deliberately "had a whole-suite run behind them" and
never "verified". These statistics are reconstructed from the transcript,
while the live check additionally requires the source fingerprint to be
unchanged, and a transcript cannot show that. Reconstruction can therefore
count a claim as backed that the live check would have rejected as stale.
Naming the weaker thing that is actually measured keeps the sentence true.
"""

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from . import evidence
from .conversation import extract_claims
from .pytest_parser import parse_summary_line
from .transcript import (
    DEFAULT_REPLAY_BUDGET_S, ReplayAborted, assistant_text, iter_records,
    record_timestamp,
)

NO_RUN = "no_run"
NARROWED_ONLY = "narrowed_only"
STALE = "stale"

# Fixed order, so equal counts always render the same way. Without this the
# message would depend on dict insertion order, which depends on the order
# turns happen to appear in the transcript.
_CAUSE_PHRASES = (
    (NO_RUN, "had no test run at all"),
    (NARROWED_ONLY, "had only narrowed runs"),
    (STALE, "had a run too old to use"),
)


@dataclass(frozen=True)
class SessionStats:
    backed: int
    unbacked: int
    causes: Mapping[str, int]

    def render(self) -> str:
        """The parenthetical appended to an unverified-claim warning.

        Empty when no claims were seen, so a caller can append it
        unconditionally.
        """
        total = self.backed + self.unbacked
        if total <= 0:
            return ""

        body = (
            f"this session: {self.backed} of {total} claims had a "
            "whole-suite run behind them"
        )

        dominant = self._dominant_cause()
        if dominant is None:
            return f"({body})"
        count, phrase = dominant
        return f"({body}; {count} {phrase})"

    def _dominant_cause(self):
        """The most common known cause as (count, phrase), or None.

        Unknown keys are ignored rather than rendered, so a cause added by a
        later version cannot produce a broken sentence here.
        """
        best = None
        for key, phrase in _CAUSE_PHRASES:
            count = self.causes.get(key, 0)
            if isinstance(count, int) and count > 0:
                if best is None or count > best[0]:
                    best = (count, phrase)
        return best


def summarize_session(
    transcript_path,
    ttl_s: float = evidence.DEFAULT_TTL_S,
    budget_s: Optional[float] = None,
) -> Optional[SessionStats]:
    """Statistics for one session, or None if they cannot be computed.

    None means "no statistics", never "no claims" and never "nothing was
    backed". Callers print their warning without the ratio rather than
    printing a number they are not sure of.
    """
    budget = DEFAULT_REPLAY_BUDGET_S if budget_s is None else budget_s

    commands = {}          # tool_use id -> the Bash command string
    saw_any_run = False
    saw_whole_suite = False
    whole_suite_at = None
    backed = 0
    causes = {}

    try:
        for record in iter_records(transcript_path, budget_s=budget):
            _register_commands(record, commands)

            if _is_test_run(record):
                saw_any_run = True
                if not _run_was_scoped(record, commands):
                    saw_whole_suite = True
                    whole_suite_at = record_timestamp(record) or whole_suite_at

            if record.get("type") != "assistant":
                continue
            if record.get("isSidechain") is True:
                # A subagent talking is not the main agent reporting.
                continue

            text = assistant_text(record)
            if not text.strip() or not extract_claims(text):
                continue

            cause = _classify(
                saw_any_run, saw_whole_suite, whole_suite_at,
                record_timestamp(record), ttl_s,
            )
            if cause is None:
                backed += 1
            else:
                causes[cause] = causes.get(cause, 0) + 1
    except ReplayAborted:
        return None

    if not _transcript_readable(transcript_path):
        return None

    return SessionStats(backed=backed, unbacked=sum(causes.values()), causes=causes)


def _transcript_readable(transcript_path) -> bool:
    """Distinguishes an empty session from an unreadable one.

    `iter_records` yields nothing in both cases by design, but "zero claims"
    and "could not read the file" must not produce the same answer here: the
    first is a real result, the second is no result.
    """
    try:
        return Path(transcript_path).is_file()
    except (TypeError, OSError):
        return False


def _register_commands(record: dict, commands: dict) -> None:
    """Records tool_use id -> command so a later result can be traced back.

    A tool_result links to its call through message.content[].tool_use_id.
    `sourceToolUseID` is null on those records and cannot be used.
    """
    message = record.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        command = (block.get("input") or {}).get("command")
        if isinstance(command, str):
            commands[block.get("id")] = command


def _is_test_run(record: dict) -> bool:
    result = record.get("toolUseResult")
    if not isinstance(result, dict):
        return False
    stdout = result.get("stdout")
    if not isinstance(stdout, str):
        return False
    return parse_summary_line(stdout) is not None


def _run_was_scoped(record: dict, commands: dict) -> bool:
    """True unless the command is positively recognised as whole-suite.

    Uses `is_scoped`'s default bias, where an unrecognised runner counts as
    scoped. That is the correct direction here: this decides whether evidence
    may back a whole-suite claim, and treating an unknown command as
    whole-suite would let a narrow run confirm a broad claim.
    """
    message = record.get("message")
    command = ""
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                command = commands.get(block.get("tool_use_id"), "")
                break
    try:
        tokens = shlex.split(command)
    except ValueError:
        return True
    if not tokens:
        return True
    return evidence.is_scoped(tokens)


def _classify(saw_any_run, saw_whole_suite, whole_suite_at, claim_at, ttl_s):
    """The cause this claim is unbacked, or None if it is backed."""
    if not saw_any_run:
        return NO_RUN
    if not saw_whole_suite:
        return NARROWED_ONLY
    if whole_suite_at is None or claim_at is None:
        # No usable clock. Staleness is unknown, and unknown is not evidence
        # the claim is backed.
        return STALE
    if claim_at - whole_suite_at > ttl_s:
        return STALE
    return None
