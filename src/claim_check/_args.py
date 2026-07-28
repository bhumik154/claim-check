"""Shared argparse helpers for the three entry points.

Kept out of runner.py so the runner stays free of CLI concerns, and out of
each entry point so the three don't drift apart.
"""

import argparse
import math


def positive_timeout(raw: str) -> float:
    """argparse `type=` callable for --timeout.

    Rejects zero and negative values deliberately, breaking this project's
    otherwise-universal fail-open rule. A non-positive timeout kills every
    run the instant it starts and then fails open, so the tool reports
    "could not verify ... allowing commit" forever while looking like it is
    working - silently verifying nothing is a worse outcome than a loud
    error about a flag the user typed wrong.

    Non-finite values (nan, inf, -inf, or an overflow like 1e400 that
    float() silently turns into inf) are rejected for the same reason, one
    level down: `value <= 0` is False for all of them, so they used to slip
    past this guard and reach subprocess.run(timeout=...) instead, which
    raises ValueError for nan and OverflowError for inf - neither caught by
    runner.py's TimeoutExpired/OSError handlers, so both fell through to the
    entry point's catch-all backstop and printed "internal error; allowing
    commit" on every single commit, forever.
    """
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"invalid float value: {raw!r}")
    if not math.isfinite(value):
        raise argparse.ArgumentTypeError(
            f"--timeout must be a finite number (got {value}); a non-finite timeout "
            "crashes the test run in a way this tool cannot catch, which silently "
            "verifies nothing on every commit"
        )
    if value <= 0:
        raise argparse.ArgumentTypeError(
            f"--timeout must be greater than 0 (got {value}); a non-positive timeout "
            "kills every run instantly and silently verifies nothing"
        )
    return value
